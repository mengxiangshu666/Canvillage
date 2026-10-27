"""Tool failure wording: parse the code before trimming, never show raw JSON."""

from __future__ import annotations

from novelvideo.chat.tool_error_messages import (
    format_tool_failure,
    tool_error_code,
    tool_error_detail,
)

RAW = (
    'Error: 远程工具 village_canvas_dispatch_action 执行失败：HTTP 400 '
    '{"ok": false, "error_code": "canvas_revision_conflict", '
    '"error": "画布版本已变化，请基于最新版本重试", "current_revision": 12, '
    '"disposition": {"disposition": "retry_after_refresh"}}'
)


def test_extracts_code_from_http_prefixed_body():
    assert tool_error_code(RAW) == "canvas_revision_conflict"
    assert "画布版本已变化" in tool_error_detail(RAW)


def test_friendly_message_replaces_raw_transport_text():
    text = format_tool_failure("执行画布操作", RAW)
    assert text.startswith("⚠️ 执行画布操作失败：")
    assert "画布在你操作期间被别的改动改过" in text
    assert "远程工具" not in text
    assert "HTTP 400" not in text
    assert "error_code" not in text


def test_extracts_code_from_plain_json_mapping():
    payload = {
        "ok": False,
        "error_code": "FZ_EMIT_UNVERIFIED",
        "error": "画布写入没有被确认",
    }
    assert tool_error_code(payload) == "FZ_EMIT_UNVERIFIED"
    assert "画布写入没有被确认" in format_tool_failure("执行画布操作", payload)


def test_extracts_code_from_nested_content_json():
    payload = {
        "ok": False,
        "content": '{"ok": false, "error_code": "canvas_revision_conflict"}',
    }
    assert tool_error_code(payload) == "canvas_revision_conflict"


def test_unknown_code_keeps_the_server_detail_without_raw_json():
    text = format_tool_failure(
        "执行画布操作",
        'HTTP 502 {"ok": false, "error": "上游网关暂时不可用"}',
    )
    assert "上游网关暂时不可用" in text
    assert "HTTP 502" not in text
    assert "{" not in text


def test_empty_output_has_a_generic_actionable_message():
    text = format_tool_failure("执行画布操作", "")
    assert "请稍后重试或换一种做法" in text
