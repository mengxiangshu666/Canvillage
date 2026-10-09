from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Literal


@dataclass(slots=True)
class ChatBackendEvent:
    type: Literal[
        "thread_started",
        "assistant_delta",
        "tool_update",
        "canvas_patch",
        "progress",
        "complete",
    ]
    thread_id: str | None = None
    turn_id: str | None = None
    text: str | None = None
    name: str | None = None
    raw: Any | None = None


@dataclass(slots=True)
class ChatRunResult:
    thread_id: str
    text: str


_LIVE_CLAUDE_CLIENTS_LOCK = threading.Lock()
_LIVE_CLAUDE_CLIENTS: dict[str, Any] = {}
_INTERRUPTED_CLAUDE_CLIENTS: set[str] = set()










def register_live_claude_client(thread_id: str, client: Any) -> None:
    key = str(thread_id or "").strip()
    if not key:
        return
    with _LIVE_CLAUDE_CLIENTS_LOCK:
        _LIVE_CLAUDE_CLIENTS[key] = client


def unregister_live_claude_client(thread_id: str) -> None:
    key = str(thread_id or "").strip()
    if not key:
        return
    with _LIVE_CLAUDE_CLIENTS_LOCK:
        _LIVE_CLAUDE_CLIENTS.pop(key, None)


async def interrupt_live_claude_client(thread_id: str) -> bool:
    key = str(thread_id or "").strip()
    if not key:
        return False
    with _LIVE_CLAUDE_CLIENTS_LOCK:
        client = _LIVE_CLAUDE_CLIENTS.get(key)
    if client is None:
        return False
    await client.interrupt()
    with _LIVE_CLAUDE_CLIENTS_LOCK:
        _INTERRUPTED_CLAUDE_CLIENTS.add(key)
    return True


def consume_interrupted_claude_client(thread_id: str) -> bool:
    key = str(thread_id or "").strip()
    if not key:
        return False
    with _LIVE_CLAUDE_CLIENTS_LOCK:
        if key not in _INTERRUPTED_CLAUDE_CLIENTS:
            return False
        _INTERRUPTED_CLAUDE_CLIENTS.discard(key)
        return True


def _extract_claude_text(data: dict[str, Any]) -> str | None:
    if data.get("type") == "result" and data.get("subtype") == "success":
        result = data.get("result")
        return result if isinstance(result, str) else None

    if data.get("type") != "assistant":
        return None

    message = data.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, list):
        return None

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(part for part in parts if part).strip() or None


def _parse_claude_stream_event(data: dict[str, Any]) -> dict[str, Any] | None:
    if data.get("type") != "stream_event":
        return None

    event = data.get("event")
    if not isinstance(event, dict):
        return None

    event_type = event.get("type")
    if event_type == "content_block_delta":
        delta = event.get("delta")
        if isinstance(delta, dict) and delta.get("type") == "text_delta":
            text = delta.get("text")
            if isinstance(text, str) and text:
                return {"type": "text_delta", "text": text}
        if isinstance(delta, dict) and delta.get("type") == "input_json_delta":
            partial = delta.get("partial_json")
            if isinstance(partial, str):
                return {"type": "tool_input_delta", "text": partial}

    if event_type == "content_block_start":
        block = event.get("content_block")
        if isinstance(block, dict) and block.get("type") == "tool_use":
            name = block.get("name") if isinstance(block.get("name"), str) else "Tool"
            tool_id = block.get("id") if isinstance(block.get("id"), str) else ""
            return {"type": "tool_start", "name": name, "tool_id": tool_id}

    return None
















def _extract_claude_sdk_assistant_text(message: Any) -> str | None:
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts).strip() or None


def _summarize_claude_tool_payload(payload: Any) -> str:
    if not isinstance(payload, dict) or not payload:
        return ""
    keys = [str(key).strip() for key in payload.keys() if str(key).strip()]
    if not keys:
        return ""
    preview = ", ".join(keys[:3])
    if len(keys) > 3:
        preview += ", ..."
    return preview


def _format_claude_tool_use_block(block: Any) -> tuple[str, str] | None:
    tool_id = str(getattr(block, "id", "") or "").strip()
    tool_name = str(getattr(block, "name", "") or "").strip()
    if not tool_id or not tool_name:
        return None
    payload_summary = _summarize_claude_tool_payload(getattr(block, "input", None))
    if payload_summary:
        text = f"[tool:start] {tool_name} ({payload_summary})\n"
    else:
        text = f"[tool:start] {tool_name}\n"
    return (f"tool_use:{tool_id}", text)


def _format_claude_tool_result_block(block: Any) -> tuple[str, str] | None:
    tool_use_id = str(getattr(block, "tool_use_id", "") or "").strip()
    if not tool_use_id:
        return None
    is_error = bool(getattr(block, "is_error", False))
    content = getattr(block, "content", None)
    summary = ""
    if isinstance(content, str):
        summary = content.strip().replace("\r\n", "\n").replace("\r", "\n")
    elif isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            summary = str(first.get("text", "") or first.get("content", "") or "").strip()
    if summary:
        summary = summary.splitlines()[0].strip()
        if len(summary) > 120:
            summary = summary[:117].rstrip() + "..."
    label = "tool:error" if is_error else "tool:done"
    text = f"[{label}] {tool_use_id}"
    if summary:
        text += f" — {summary}"
    return (f"tool_result:{tool_use_id}", text + "\n")


def _collect_claude_message_traces(message: Any) -> list[tuple[str, str]]:
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return []
    traces: list[tuple[str, str]] = []
    for block in content:
        trace = _format_claude_tool_use_block(block)
        if trace:
            traces.append(trace)
            continue
        trace = _format_claude_tool_result_block(block)
        if trace:
            traces.append(trace)
    return traces


def _extract_claude_sdk_session_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("session_id", "sessionId", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None
    for key in ("session_id", "sessionId", "id"):
        value = getattr(payload, key, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _format_claude_system_trace(message: Any) -> str | None:
    subtype = str(getattr(message, "subtype", "") or "").strip()
    data = getattr(message, "data", None)
    if subtype == "task_started":
        description = str(getattr(message, "description", "") or "").strip()
        task_type = str(getattr(message, "task_type", "") or "").strip()
        label = task_type or "task"
        if description:
            return f"[task:start] {label} — {description}\n"
        return f"[task:start] {label}\n"
    if subtype == "task_progress":
        description = str(getattr(message, "description", "") or "").strip()
        last_tool_name = str(getattr(message, "last_tool_name", "") or "").strip()
        if description and last_tool_name:
            return f"[task:progress] {last_tool_name} — {description}\n"
        if description:
            return f"[task:progress] {description}\n"
        if last_tool_name:
            return f"[tool] {last_tool_name}\n"
        return None
    if subtype == "task_notification":
        status = str(getattr(getattr(message, "status", None), "value", getattr(message, "status", "")) or "").strip()
        summary = str(getattr(message, "summary", "") or "").strip()
        output_file = str(getattr(message, "output_file", "") or "").strip()
        text = f"[task:{status or 'update'}]"
        if summary:
            text += f" {summary}"
        if output_file:
            text += f"\n[file] {output_file}"
        return text.rstrip() + "\n"
    if subtype in {"init", "initialized", "ready"}:
        return None
    if subtype:
        parts: list[str] = []
        if isinstance(data, dict):
            for key in ("message", "summary", "description"):
                value = str(data.get(key, "") or "").strip()
                if value:
                    parts.append(value)
                    break
        if not parts:
            return None
        suffix = f" {' — '.join(parts)}"
        return f"[system:{subtype}]{suffix}\n"
    return None


class ClaudeSdkClient:
    def __init__(self, *, cli_path: Path, cwd: Path, env: dict[str, str], model: str | None) -> None:
        self._cli_path = cli_path
        self._cwd = cwd
        self._env = env
        self._model = str(model or "").strip() or None

    def thread_start(self) -> "ClaudeSdkThread":
        return ClaudeSdkThread(
            cli_path=self._cli_path,
            cwd=self._cwd,
            env=self._env,
            model=self._model,
            thread_id=None,
            is_new=True,
        )

    def thread_resume(self, thread_id: str) -> "ClaudeSdkThread":
        return ClaudeSdkThread(
            cli_path=self._cli_path,
            cwd=self._cwd,
            env=self._env,
            model=self._model,
            thread_id=thread_id,
            is_new=False,
        )


class ClaudeSdkThread:
    def __init__(
        self,
        *,
        cli_path: Path,
        cwd: Path,
        env: dict[str, str],
        model: str | None,
        thread_id: str | None,
        is_new: bool,
    ) -> None:
        self._cli_path = cli_path
        self._cwd = cwd
        self._env = env
        self._model = str(model or "").strip() or None
        self.id = str(thread_id or "").strip()
        self._is_new = is_new

    async def stream(self, prompt: str) -> AsyncIterator[ChatBackendEvent]:
        try:
            from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
            from claude_agent_sdk.types import (
                AssistantMessage,
                ResultMessage,
                StreamEvent,
                SystemMessage,
                TaskNotificationMessage,
                TaskProgressMessage,
                TaskStartedMessage,
                UserMessage,
            )
        except ImportError as exc:
            raise RuntimeError("claude-agent-sdk is not installed") from exc

        options = ClaudeAgentOptions(
            cwd=str(self._cwd),
            cli_path=str(self._cli_path),
            env=self._env,
            include_partial_messages=True,
            permission_mode="bypassPermissions",
            tools={"type": "preset", "preset": "claude_code"},
            system_prompt={"type": "preset", "preset": "claude_code"},
            setting_sources=["user", "project", "local"],
            model=self._model,
            resume=(self.id or None) if not self._is_new else None,
        )

        client = ClaudeSDKClient(options=options)
        assistant_parts: list[str] = []
        tool_lines: list[str] = []
        seen_tool_traces: set[str] = set()
        final_result: str | None = None
        provisional_id = self.id
        try:
            await client.connect()
            server_info = await client.get_server_info()
            session_id = _extract_claude_sdk_session_id(server_info)
            if session_id:
                self.id = session_id
            elif not self.id:
                self.id = f"claude-{id(client)}"

            provisional_id = self.id
            register_live_claude_client(self.id, client)
            yield ChatBackendEvent(type="thread_started", thread_id=self.id)

            await client.query(prompt)

            async for message in client.receive_response():
                if isinstance(message, StreamEvent):
                    stream_event = _parse_claude_stream_event(getattr(message, "event", None) or {})
                    if stream_event:
                        if stream_event["type"] == "text_delta":
                            assistant_parts.append(stream_event["text"])
                            yield ChatBackendEvent(
                                type="assistant_delta",
                                thread_id=self.id,
                                text="".join(assistant_parts),
                            )
                        elif stream_event["type"] == "tool_start":
                            tool_lines.append(f"调用工具：{stream_event['name']}")
                            yield ChatBackendEvent(
                                type="tool_update",
                                thread_id=self.id,
                                text="\n".join(tool_lines),
                            )
                        elif stream_event["type"] == "tool_input_delta" and tool_lines:
                            tool_lines[-1] = tool_lines[-1] + stream_event["text"]
                            yield ChatBackendEvent(
                                type="tool_update",
                                thread_id=self.id,
                                text="\n".join(tool_lines),
                            )
                    continue

                if isinstance(
                    message,
                    (TaskStartedMessage, TaskProgressMessage, TaskNotificationMessage, SystemMessage),
                ):
                    trace = _format_claude_system_trace(message)
                    if trace:
                        tool_lines.append(trace.rstrip("\n"))
                        yield ChatBackendEvent(
                            type="tool_update",
                            thread_id=self.id,
                            text="\n".join(tool_lines),
                        )
                    continue

                if isinstance(message, AssistantMessage):
                    for trace_key, trace_text in _collect_claude_message_traces(message):
                        if trace_key in seen_tool_traces:
                            continue
                        seen_tool_traces.add(trace_key)
                        tool_lines.append(trace_text.rstrip("\n"))
                        yield ChatBackendEvent(
                            type="tool_update",
                            thread_id=self.id,
                            text="\n".join(tool_lines),
                        )
                    extracted = _extract_claude_sdk_assistant_text(message)
                    if extracted:
                        final_result = extracted
                    continue

                if isinstance(message, UserMessage):
                    for trace_key, trace_text in _collect_claude_message_traces(message):
                        if trace_key in seen_tool_traces:
                            continue
                        seen_tool_traces.add(trace_key)
                        tool_lines.append(trace_text.rstrip("\n"))
                        yield ChatBackendEvent(
                            type="tool_update",
                            thread_id=self.id,
                            text="\n".join(tool_lines),
                        )
                    continue

                if isinstance(message, ResultMessage):
                    session_id = _extract_claude_sdk_session_id(message)
                    if session_id and session_id != self.id:
                        unregister_live_claude_client(self.id)
                        self.id = session_id
                        register_live_claude_client(self.id, client)
                    if isinstance(message.result, str) and message.result.strip():
                        final_result = message.result.strip()
                    interrupted = consume_interrupted_claude_client(self.id)
                    if interrupted:
                        yield ChatBackendEvent(
                            type="complete",
                            thread_id=self.id,
                            text=(final_result or "".join(assistant_parts)).strip() or "已中断。",
                        )
                        return

            assistant_text = (final_result or "".join(assistant_parts)).strip() or "已执行，但没有返回正文。"
            yield ChatBackendEvent(type="complete", thread_id=self.id, text=assistant_text)
        finally:
            unregister_live_claude_client(self.id or provisional_id)
            try:
                await client.disconnect()
            except Exception:
                pass

    async def run(self, prompt: str) -> ChatRunResult:
        text = ""
        async for event in self.stream(prompt):
            if event.type == "complete":
                text = event.text or ""
        return ChatRunResult(thread_id=self.id, text=text)


class ClaudeCliClient:
    def __init__(self, *, cli_path: Path, cwd: Path, env: dict[str, str]) -> None:
        self._cli_path = cli_path
        self._cwd = cwd
        self._env = env

    def thread_start(self, thread_id: str) -> "ClaudeCliThread":
        return ClaudeCliThread(
            cli_path=self._cli_path,
            cwd=self._cwd,
            env=self._env,
            thread_id=thread_id,
            is_new=True,
        )

    def thread_resume(self, thread_id: str) -> "ClaudeCliThread":
        return ClaudeCliThread(
            cli_path=self._cli_path,
            cwd=self._cwd,
            env=self._env,
            thread_id=thread_id,
            is_new=False,
        )


class ClaudeCliThread:
    def __init__(
        self,
        *,
        cli_path: Path,
        cwd: Path,
        env: dict[str, str],
        thread_id: str,
        is_new: bool,
    ) -> None:
        self._cli_path = cli_path
        self._cwd = cwd
        self._env = env
        self.id = thread_id
        self._is_new = is_new

    async def stream(self, prompt: str) -> AsyncIterator[ChatBackendEvent]:
        if not self._cli_path.exists():
            raise RuntimeError(f"Claude CLI not found: {self._cli_path}")

        cmd = [str(self._cli_path)]
        if self._is_new:
            cmd.extend(["--session-id", self.id])
        else:
            cmd.extend(["--resume", self.id])
        cmd.extend(
            [
                "--output-format",
                "stream-json",
                "--include-partial-messages",
                "--verbose",
                "--dangerously-skip-permissions",
                "-p",
                prompt,
            ]
        )

        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self._cwd),
            env=self._env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        yield ChatBackendEvent(type="thread_started", thread_id=self.id)

        assistant_parts: list[str] = []
        tool_lines: list[str] = []
        final_result: str | None = None

        try:
            assert process.stdout is not None
            async for raw_line in process.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                stream_event = _parse_claude_stream_event(data)
                if stream_event:
                    if stream_event["type"] == "text_delta":
                        assistant_parts.append(stream_event["text"])
                        yield ChatBackendEvent(
                            type="assistant_delta",
                            thread_id=self.id,
                            text="".join(assistant_parts),
                        )
                    elif stream_event["type"] == "tool_start":
                        tool_lines.append(f"调用工具：{stream_event['name']}")
                        yield ChatBackendEvent(
                            type="tool_update",
                            thread_id=self.id,
                            text="\n".join(tool_lines),
                        )
                    elif stream_event["type"] == "tool_input_delta" and tool_lines:
                        tool_lines[-1] = tool_lines[-1] + stream_event["text"]
                        yield ChatBackendEvent(
                            type="tool_update",
                            thread_id=self.id,
                            text="\n".join(tool_lines),
                        )
                    continue

                extracted = _extract_claude_text(data)
                if extracted:
                    final_result = extracted

            stderr_text = ""
            if process.stderr is not None:
                stderr_text = (await process.stderr.read()).decode("utf-8", errors="replace").strip()
            return_code = await process.wait()

            if return_code != 0:
                raise RuntimeError(stderr_text or final_result or f"Claude exited with code {return_code}")

            assistant_text = (
                final_result or "".join(assistant_parts)
            ).strip() or "已执行，但没有返回正文。"
            yield ChatBackendEvent(type="complete", thread_id=self.id, text=assistant_text)
        finally:
            if process.returncode is None:
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=5)
                except (asyncio.TimeoutError, ProcessLookupError):
                    process.kill()

    async def run(self, prompt: str) -> ChatRunResult:
        text = ""
        async for event in self.stream(prompt):
            if event.type == "complete":
                text = str(event.text or "").strip()
        return ChatRunResult(thread_id=self.id, text=text or "已执行，但没有返回正文。")
