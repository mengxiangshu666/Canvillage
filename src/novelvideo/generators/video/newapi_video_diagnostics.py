"""Credential-safe diagnostics for NewAPI video transport failures."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class NewApiVideoError(RuntimeError):
    """NewAPI video request failure with gateway request id when available."""

    def __init__(
        self,
        message: str,
        *,
        request_id: str = "",
        http_status: int | None = None,
        response_text: str = "",
        stage: str = "",
        url_path: str = "",
        submission_result_unknown: bool = False,
        request_contract: dict[str, Any] | None = None,
        redirect_location: str = "",
        attempted_routes: Iterable[str] = (),
    ):
        super().__init__(message)
        self.request_id = request_id
        self.http_status = http_status
        self.response_text = response_text
        self.stage = stage
        self.url_path = url_path
        self.submission_result_unknown = submission_result_unknown
        self.request_contract = dict(request_contract or {})
        #: Already credential-redacted ``Location`` of a refusal redirect.  Kept
        #: so the diagnostic can name the route the gateway wanted instead of
        #: reporting a bare status.
        self.redirect_location = str(redirect_location or "")
        #: Create routes already tried for this submit.  Reported so a 404
        #: points at the routes that were actually walked rather than telling
        #: the operator to re-run a probe that cannot see the difference.
        self.attempted_routes = tuple(
            dict.fromkeys(str(item).strip() for item in attempted_routes if str(item).strip())
        )

    @property
    def is_redirect(self) -> bool:
        """Return whether the gateway answered with an unfollowed redirect."""
        return self.http_status in {301, 302, 303, 307, 308}

    @property
    def endpoint_class(self) -> str:
        path = str(self.url_path or "").lower()
        if self.stage == "serialize":
            return "video-request-serialization"
        if self.stage == "submit":
            return "video-submit"
        if self.stage == "query":
            return "video-task-query"
        if self.stage == "download":
            return "video-result-download"
        if "content" in path or "download" in path:
            return "video-result-download"
        return "video-transport"

    def diagnostic_contract(self, *, protocol: str = "") -> dict[str, object]:
        status = self.http_status
        error_text = f"{self}\n{self.response_text}".casefold()
        request_contract = self.request_contract
        body_bytes = request_contract.get("body_bytes")
        content_length = request_contract.get("content_length")
        local_body_complete = (
            isinstance(body_bytes, int)
            and isinstance(content_length, int)
            and body_bytes > 0
            and body_bytes == content_length
        )
        relay_json_body_rejected = (
            status == 400
            and "canonicalize json" in error_text
            and "unexpected end" in error_text
        )
        # NewAPI answers a model whose group has no live channel with this
        # exact envelope.  It is provisioning, not a transient outage: the
        # group assignment lives on the relay account, so no amount of
        # retrying or parameter fixing will change the answer.
        channel_unavailable_group = (
            _channel_group_from_error(f"{self}\n{self.response_text}")
            if "no available channel" in error_text
            else ""
        )
        channel_unavailable = bool(channel_unavailable_group) or (
            status == 503 and "no available channel" in error_text
        )
        html_response = "text/html" in error_text or self.response_text.lstrip().startswith("<")
        empty_response = not self.response_text.strip() and status in {200, 201, 202, 204}
        if self.stage == "serialize":
            error_code = "VIDEO_REQUEST_SERIALIZATION_FAILED"
            suggested_action = "检查请求参数是否包含不可序列化值后重试。"
        elif self.is_redirect and self.stage != "download":
            # A refusal redirect used to be followed silently, and aiohttp
            # rewrites ``POST`` to ``GET`` on 301/302 — so the gateway answered
            # an unfollowed GET for a path the caller never requested and the
            # real cause (a moved create route) was reported as a bare 404.
            # Name the observed route instead of sending the operator back to
            # a probe that only re-reads ``GET /models``.
            error_code = "VIDEO_SUBMIT_ROUTE_REDIRECTED"
            observed = str(self.redirect_location or "").strip()
            detail = f"网关把提交路由重定向到 {observed}；" if observed else ""
            suggested_action = (
                f"{detail}当前提交路径未被该网关注册，请改用该模型已实测可用的提交路由"
                "（或按模型能力表修正 Base URL）后再生成。"
            )
        elif relay_json_body_rejected and local_body_complete:
            error_code = "VIDEO_RELAY_JSON_BODY_REJECTED"
            suggested_action = (
                "本地已生成完整 JSON 请求体；优先检查 NewAPI/中转层是否截断、"
                "未读取或错误转发 body，修复网关后再重试。"
            )
        elif relay_json_body_rejected:
            error_code = "VIDEO_JSON_BODY_REJECTED"
            suggested_action = (
                "检查请求体是否在本地传输或 NewAPI/中转层被截断，"
                "确认 body 长度后再重试。"
            )
        elif html_response:
            if self.stage == "download":
                error_code = "VIDEO_RESULT_HTML_RESPONSE"
                suggested_action = (
                    "视频结果地址返回了 HTML 页面；检查结果 CDN、统一 content 端点和中转站路由。"
                )
            else:
                error_code = "VIDEO_ENDPOINT_HTML_RESPONSE"
                suggested_action = (
                    "视频接口返回了 HTML 管理页面；检查 Base URL、/videos 提交路径和中转站路由，"
                    "不要把管理后台地址当作视频 API 地址。"
                )
        elif empty_response:
            if self.stage == "download":
                error_code = "VIDEO_RESULT_DOWNLOAD_FAILED"
                suggested_action = "视频结果下载返回空内容；检查结果端点和中转层响应完整性。"
            else:
                error_code = "VIDEO_EMPTY_RESPONSE"
                suggested_action = (
                    "视频接口返回空响应；检查中转层是否截断响应、Content-Type 和提交路径，"
                    "保留请求合同后再重试。"
                )
        elif self.submission_result_unknown:
            error_code = "VIDEO_SUBMIT_RESULT_UNKNOWN"
            suggested_action = (
                "渠道可能已经接收任务；请保留当前节点，稍后使用任务恢复，"
                "不要立即再次创建任务。"
            )
        elif status in {401, 403}:
            error_code = "VIDEO_AUTH_REJECTED"
            suggested_action = "检查该模型的 URL、Key、鉴权协议和上游账号权限后重新检测。"
        elif status in {404, 405}:
            if self.stage == "download":
                error_code = "VIDEO_RESULT_DOWNLOAD_FAILED"
                suggested_action = "视频结果端点返回 404/405；检查 CDN URL、content 路由和结果网关。"
            else:
                error_code = "VIDEO_ENDPOINT_NOT_FOUND"
                tried = "、".join(self.attempted_routes)
                suggested_action = (
                    f"该网关未注册已尝试的提交路由（{tried}）；"
                    "请改用该模型已实测可用的提交路由后再生成。"
                    if tried
                    else "重新检测模型协议与提交路径，保存识别结果后再生成。"
                )
        elif status == 429:
            error_code = "VIDEO_RATE_LIMITED"
            suggested_action = "稍后重试，或切换到有可用额度的同模型渠道。"
        elif channel_unavailable:
            # Without this branch the operator sees a plain 503 and "稍后重试",
            # which is wrong twice: the model directory advertises the model, so
            # there is nothing to fix on our side, and the missing piece is the
            # relay account's group assignment for the model.
            error_code = "VIDEO_CHANNEL_UNAVAILABLE"
            group = f"（分组 {channel_unavailable_group}）" if channel_unavailable_group else ""
            suggested_action = (
                f"该中转站的模型目录里有这个模型，但当前账号{group}下没有可用渠道，"
                "属于中转站侧的渠道配置/配额问题，重试和改参数都不会改变结果；"
                "请联系中转站开通该模型渠道，或切换到别的已实测可用渠道。"
            )
        elif status is not None and 400 <= status < 500:
            if self.stage == "download":
                error_code = "VIDEO_RESULT_DOWNLOAD_FAILED"
                suggested_action = "视频结果端点拒绝下载；检查结果 URL、content 路由和鉴权。"
            else:
                error_code = "VIDEO_REQUEST_REJECTED"
                suggested_action = "按模型能力表修正尺寸、时长、参考素材或声音参数后重试。"
        elif status is not None and status >= 500:
            if self.stage == "download":
                error_code = "VIDEO_RESULT_DOWNLOAD_FAILED"
                suggested_action = "视频结果端点暂时不可用；保留任务并稍后恢复下载。"
            else:
                error_code = "VIDEO_UPSTREAM_UNAVAILABLE"
                suggested_action = "保留当前节点并稍后重试；系统会继续使用已配置的同模型路由。"
        else:
            error_code = "VIDEO_TRANSPORT_FAILED"
            suggested_action = "检查网络与渠道连通性后重试。"
        result: dict[str, object] = {
            "error_code": error_code,
            "http_status": status,
            "stage": self.stage or "transport",
            "protocol": str(protocol or "").strip(),
            "endpoint_class": self.endpoint_class,
            "retryable": (
                self.stage != "serialize"
                and not channel_unavailable
                and (
                    self.submission_result_unknown
                    or status in {408, 409, 425, 429}
                    or status is None
                    or bool(status >= 500)
                )
            ),
            "suggested_action": suggested_action,
            "request_id": self.request_id or None,
        }
        if channel_unavailable and channel_unavailable_group:
            result["channel_group"] = channel_unavailable_group
        if self.redirect_location:
            result["redirect_location"] = self.redirect_location
        if self.attempted_routes:
            result["attempted_routes"] = list(self.attempted_routes)
        if self.request_contract:
            result["request_contract"] = dict(self.request_contract)
        return result


_CHANNEL_GROUP_PATTERN = re.compile(
    r"no available channel for model\s+[^\s]*\s+under group\s+(?P<group>.+?)\s*"
    r"(?:\(|$)",
    re.IGNORECASE,
)


def _channel_group_from_error(text: str) -> str:
    """Return the NewAPI group a model was resolved under, when it says so.

    ``... under group CN-H3 (distributor) ...`` — the group names the relay's
    own routing bucket, so quoting it back is what lets the operator tell their
    relay what to fix.
    """

    match = _CHANNEL_GROUP_PATTERN.search(str(text or ""))
    if not match:
        return ""
    return match.group("group").strip()[:80]


def extract_request_id(text: str = "", headers: object | None = None) -> str:
    """Extract an upstream request ID from standard headers or error payloads."""
    if headers:
        for header_name in (
            "x-request-id",
            "x-newapi-request-id",
            "x-oneapi-request-id",
        ):
            value = getattr(headers, "get", lambda _name: "")(header_name)
            if value:
                return str(value)

    if text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            error_obj = data.get("error") if isinstance(data.get("error"), dict) else {}
            for candidate in (
                data.get("request_id"),
                data.get("requestId"),
                error_obj.get("request_id"),
                error_obj.get("requestId"),
            ):
                if isinstance(candidate, str) and candidate:
                    return candidate
            text = str(error_obj.get("message") or text)

        match = re.search(
            r"request[\s_-]*id[:：]\s*([A-Za-z0-9_-]+)", text, re.IGNORECASE
        )
        if match:
            return match.group(1)

    return ""


def safe_url_for_log(url: str) -> str:
    """Return a URL with credential-like query values and fragments redacted."""
    parsed = urlsplit(str(url or ""))
    sensitive_keys = {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "key",
        "sig",
        "signature",
        "token",
    }
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    safe_query = urlencode(
        [
            (key, "***" if key.strip().lower() in sensitive_keys else value)
            for key, value in query_pairs
        ],
        doseq=True,
    )
    safe_fragment = "***" if parsed.fragment else ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, safe_query, safe_fragment))


def transport_error_message(
    stage: str,
    url: str,
    exc: BaseException,
    *,
    redact_url: Callable[[str], str] = safe_url_for_log,
) -> str:
    """Format a supportable transport error without exposing URL credentials."""
    error_text = str(exc).strip() or repr(exc)
    return (
        f"Village Infinite Canvas API {stage} transport failed: "
        f"{type(exc).__name__}: {error_text}; url={redact_url(url)}"
    )
