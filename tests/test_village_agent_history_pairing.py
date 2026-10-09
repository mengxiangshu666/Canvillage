"""The retained Village transcript must always be a shape providers accept.

A ``role: tool`` message is only legal when the assistant ``tool_calls`` it
answers is in the same request.  Windowing the transcript by message count used
to cut that pair apart, which is what made ``deepseek-flash`` answer
``Messages with role 'tool' must be a response to a preceding message with
'tool_calls'`` and kept every later turn failing on the same request.
"""

from __future__ import annotations

import json
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from novelvideo.chat.backend_sdk import ChatBackendEvent
from novelvideo.chat.village_harness import (
    _HISTORY_MAX_MESSAGES,
    _bounded_history,
    _flatten_tool_results,
    _is_tool_result_part,
    _is_unpaired_tool_message_error,
    _message_opens_context,
    VillageAgentThread,
)

_REJECTION_BODY = {
    "message": (
        "Messages with role 'tool' must be a response to a preceding message "
        "with 'tool_calls'"
    ),
    "type": "invalid_request_error",
    "param": None,
    "code": "invalid_request_error",
}


def _pair_rejection() -> ModelHTTPError:
    return ModelHTTPError(400, "deepseek-flash", dict(_REJECTION_BODY))


def _tool_transcript(turns: int, calls_per_turn: int = 1):
    """A realistic transcript: user -> tool call -> tool result -> answer."""

    messages: list[object] = []
    call_id = 0
    for turn in range(turns):
        messages.append(ModelRequest(parts=[UserPromptPart(content=f"用户 {turn}")]))
        for step in range(calls_per_turn):
            identifier = f"call-{call_id}"
            messages.append(
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="village_canvas_read_compact",
                            args={"turn": turn, "step": step},
                            tool_call_id=identifier,
                        )
                    ]
                )
            )
            messages.append(
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="village_canvas_read_compact",
                            content={"ok": True, "revision": call_id},
                            tool_call_id=identifier,
                        )
                    ]
                )
            )
            call_id += 1
        messages.append(ModelResponse(parts=[TextPart(content=f"回答 {turn}")]))
    return messages


async def _openai_payload(messages) -> list[dict]:
    """Map a history exactly the way the OpenAI-compatible provider sees it."""

    model = OpenAIChatModel(
        "deepseek-flash",
        provider=OpenAIProvider(api_key="test", base_url="http://127.0.0.1:9/v1"),
    )
    return await model._map_messages(messages, ModelRequestParameters())


def _assert_provider_shaped(payload: list[dict]) -> None:
    assert payload, "history mapped to an empty request"
    assert payload[0]["role"] in {"system", "user"}, payload[0]["role"]
    assert _first_orphan(payload) is None, "orphan tool message"


def _first_orphan(payload: list[dict]) -> dict | None:
    """Return the first ``role: tool`` message that answers nothing."""

    declared: set[str] = set()
    for message in payload:
        if message["role"] == "assistant":
            declared.update(
                str(call.get("id") or "") for call in message.get("tool_calls") or []
            )
        elif message["role"] == "tool":
            if message.get("tool_call_id") not in declared:
                return message
    return None


def _legacy_window(messages):
    """The pre-fix window: a plain ``[-N:]`` cut, which produced the 400."""

    import novelvideo.chat.village_harness as harness

    bounded = list(messages)[-harness._HISTORY_MAX_MESSAGES :]
    total_chars = sum(len(str(message)) for message in bounded)
    while len(bounded) > 1 and total_chars > harness._HISTORY_MAX_CHARS:
        total_chars -= len(str(bounded.pop(0)))
    return bounded


class _PairingEndpoint:
    """A local OpenAI-compatible endpoint that enforces DeepSeek's tool pairing."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []
        self.orphans: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.bodies.append(body)
                orphan = _first_orphan(body.get("messages") or [])
                if orphan is not None:
                    outer.orphans.append(orphan)
                    self._write(
                        400,
                        json.dumps(_REJECTION_BODY).encode(),
                        content_type="application/json",
                    )
                    return
                self._write(200, _sse_text("已恢复"), content_type="text/event-stream")

            def _write(self, status: int, payload: bytes, *, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _sse_text(text: str) -> bytes:
    def chunk(delta: dict, finish: str | None = None) -> str:
        return "data: " + json.dumps(
            {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "deepseek-flash",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
        ) + "\n\n"

    return (
        chunk({"role": "assistant", "content": text})
        + chunk({}, finish="stop")
        + "data: [DONE]\n\n"
    ).encode()


async def test_window_never_opens_on_a_tool_message() -> None:
    history = _tool_transcript(turns=8)

    bounded = _bounded_history(history)

    assert len(bounded) <= _HISTORY_MAX_MESSAGES
    assert _message_opens_context(bounded[0])
    _assert_provider_shaped(await _openai_payload(bounded))


async def test_window_stays_provider_shaped_for_any_cut_point() -> None:
    for seed in range(120):
        rng = random.Random(seed)
        history = _tool_transcript(
            turns=rng.randint(4, 12),
            calls_per_turn=rng.randint(0, 3),
        )
        bounded = _bounded_history(history)
        payload = await _openai_payload(bounded)
        _assert_provider_shaped(payload)


async def test_result_whose_call_is_gone_survives_as_text() -> None:
    # A transcript that already starts mid-exchange, as an older build stored it.
    orphan = ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="village_canvas_read_compact",
                content={"ok": True, "revision": 42, "node_count": 156},
                tool_call_id="call-lost",
            )
        ]
    )
    history = [orphan, ModelResponse(parts=[TextPart(content="回答")])]

    bounded = _bounded_history(history)

    assert _message_opens_context(bounded[0])
    assert not any(
        _is_tool_result_part(part)
        for message in bounded
        for part in getattr(message, "parts", ()) or ()
    )
    rendered = str(bounded[0])
    assert "HISTORY_TOOL_RESULT" in rendered
    assert "call-lost" not in rendered
    assert "village_canvas_read_compact" in rendered
    assert "156" in rendered
    _assert_provider_shaped(await _openai_payload(bounded))


async def test_char_budget_still_bounds_the_window() -> None:
    history = _tool_transcript(turns=60)
    padded = [
        message
        if str(getattr(message, "kind", "")) != "request"
        else ModelRequest(
            parts=[
                UserPromptPart(content=str(part.content) + "x" * 40_000)
                if isinstance(part, UserPromptPart)
                else part
                for part in message.parts
            ]
        )
        for message in history
    ]

    bounded = _bounded_history(padded)

    assert len(bounded) < len(padded)
    assert _message_opens_context(bounded[0])
    _assert_provider_shaped(await _openai_payload(bounded))


async def test_flatten_keeps_every_fact_and_drops_the_tool_protocol() -> None:
    history = _tool_transcript(turns=2)

    flattened = _flatten_tool_results(history)

    assert not any(
        str(getattr(part, "part_kind", "") or "") in {"tool-call", "tool-return"}
        for message in flattened
        for part in getattr(message, "parts", ()) or ()
    )
    rendered = "\n".join(str(message) for message in flattened)
    assert "回答 1" in rendered
    assert "HISTORY_TOOL_RESULT" in rendered
    _assert_provider_shaped(await _openai_payload(flattened))


def test_provider_rejection_is_recognised() -> None:
    assert _is_unpaired_tool_message_error(_pair_rejection())
    assert not _is_unpaired_tool_message_error(
        ModelHTTPError(
            400,
            "deepseek-flash",
            {"message": "Thinking mode does not support this tool_choice"},
        )
    )
    assert not _is_unpaired_tool_message_error(
        RuntimeError("tool 'village_canvas_read_compact' timed out")
    )
    wrapped = RuntimeError("stream failed")
    wrapped.__cause__ = _pair_rejection()
    assert _is_unpaired_tool_message_error(wrapped)


async def test_stream_retries_once_before_anything_was_streamed(monkeypatch) -> None:
    calls = {"count": 0}

    async def fake_stream_turn(self, prompt, **_kwargs):
        calls["count"] += 1
        yield ChatBackendEvent(type="thread_started", thread_id=self.id, turn_id="t1")
        if calls["count"] == 1:
            raise _pair_rejection()
        yield ChatBackendEvent(
            type="complete", thread_id=self.id, turn_id="t1", text="已按扁平历史继续"
        )

    monkeypatch.setattr(VillageAgentThread, "_stream_turn", fake_stream_turn)
    thread = VillageAgentThread(id="village-test", model_id="m", scope_kind="home")
    thread._history = _tool_transcript(turns=2)

    events = [event async for event in thread.stream("继续")]

    assert calls["count"] == 2
    assert [event.type for event in events] == ["thread_started", "complete"]
    assert not any(
        str(getattr(part, "part_kind", "") or "") in {"tool-call", "tool-return"}
        for message in thread._history
        for part in getattr(message, "parts", ()) or ()
    )


async def test_stream_does_not_retry_after_output_reached_the_user(monkeypatch) -> None:
    calls = {"count": 0}

    async def fake_stream_turn(self, prompt, **_kwargs):
        calls["count"] += 1
        yield ChatBackendEvent(type="thread_started", thread_id=self.id, turn_id="t1")
        yield ChatBackendEvent(
            type="assistant_delta", thread_id=self.id, turn_id="t1", text="部分输出"
        )
        raise _pair_rejection()

    monkeypatch.setattr(VillageAgentThread, "_stream_turn", fake_stream_turn)
    thread = VillageAgentThread(id="village-test", model_id="m", scope_kind="home")
    thread._history = _tool_transcript(turns=2)

    with pytest.raises(ModelHTTPError):
        [event async for event in thread.stream("继续")]

    assert calls["count"] == 1


async def test_real_http_provider_rejects_the_old_window_and_accepts_the_fixed_one(
    monkeypatch,
) -> None:
    """The reported 400, reproduced and closed against a real HTTP endpoint."""

    import httpx

    endpoint = _PairingEndpoint()
    try:
        # A transcript whose turns are not all the same length: the pre-fix
        # ``[-24:]`` cut then lands exactly on a tool result whose assistant call
        # fell out of the window, which is how a long session broke.
        legacy_source = _tool_transcript(turns=7) + _tool_transcript(
            turns=1, calls_per_turn=2
        )
        legacy_payload = await _openai_payload(_legacy_window(legacy_source))
        assert _first_orphan(legacy_payload) is not None, "precondition: old cut is broken"

        # The endpoint answers exactly what the provider answered the user.
        response = httpx.post(
            f"{endpoint.base_url}/chat/completions",
            json={"model": "deepseek-flash", "messages": legacy_payload},
            timeout=30,
        )
        assert response.status_code == 400
        assert "role 'tool'" in response.text
        assert endpoint.orphans, "endpoint did not see the orphan"

        # The fixed window maps the same transcript into an accepted request.
        fixed_payload = await _openai_payload(_bounded_history(legacy_source))
        accepted = httpx.post(
            f"{endpoint.base_url}/chat/completions",
            json={"model": "deepseek-flash", "messages": fixed_payload},
            timeout=30,
        )
        assert accepted.status_code == 200

        # And a full harness turn reaches the provider through the same window.
        from novelvideo.chat import village_harness

        model = OpenAIChatModel(
            "deepseek-flash",
            provider=OpenAIProvider(base_url=endpoint.base_url, api_key="test"),
        )
        monkeypatch.setattr(
            village_harness, "resolve_village_agent_model", lambda _id: "m"
        )
        monkeypatch.setattr(
            village_harness, "get_direct_pydantic_model", lambda *_args, **_kwargs: model
        )
        thread = VillageAgentThread(id="village-test", model_id="m", scope_kind="home")
        thread._history = legacy_source

        events = [event async for event in thread.stream("继续", turn_id="t1")]

        assert events[-1].type == "complete"
        assert len(endpoint.orphans) == 1  # only the deliberate legacy probe
        orphaned = [
            index
            for index, body in enumerate(endpoint.bodies)
            if _first_orphan(body.get("messages") or [])
        ]
        assert orphaned == [0], "the harness sent an unpaired tool message"
        assert len(endpoint.bodies) > 1, "the harness turn never reached the provider"
    finally:
        endpoint.close()


async def test_real_agent_turn_recovers_from_a_rejected_transcript(monkeypatch) -> None:
    """The whole turn runs twice: the second request carries no tool protocol."""

    seen: list[list[object]] = []

    async def stream_function(messages, agent_info: AgentInfo):
        seen.append(list(messages))
        if len(seen) == 1:
            raise _pair_rejection()
        yield "已恢复"

    model = FunctionModel(stream_function=stream_function)

    from novelvideo.chat import village_harness

    monkeypatch.setattr(village_harness, "resolve_village_agent_model", lambda _id: "m")
    monkeypatch.setattr(
        village_harness, "get_direct_pydantic_model", lambda *_args, **_kwargs: model
    )

    thread = VillageAgentThread(
        id="village-test", model_id="m", scope_kind="home", conversation_id="default"
    )
    thread._history = _tool_transcript(turns=2)

    events = [event async for event in thread.stream("继续", turn_id="t1")]

    types = [event.type for event in events]
    assert len(seen) == 2
    assert types[0] == "thread_started"
    assert types[-1] == "complete"
    assert types.count("thread_started") == 1
    assert events[-1].text == "已恢复"
    assert not any(
        str(getattr(part, "part_kind", "") or "") in {"tool-call", "tool-return"}
        for message in seen[1]
        for part in getattr(message, "parts", ()) or ()
    )
    assert "HISTORY_TOOL_RESULT" in "\n".join(str(message) for message in seen[1])
