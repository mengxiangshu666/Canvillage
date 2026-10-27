"""Internal implementation part for the chat service facade."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import mimetypes
from pathlib import Path
from typing import Any

_ACTIVE_TURNS: dict[str, set[asyncio.Task[Any]]] = {}
_MAX_INLINE_REFERENCE_BYTES = 64 * 1024


def _reference_image_locators(
    image_parts: list[dict[str, str]] | None,
    *,
    project: str,
    project_dir: str | Path | None,
) -> list[str]:
    """Turn vision parts into locators the sidecar can fetch through the API."""

    from novelvideo.utils.static_urls import project_static_url

    locators: list[str] = []
    for part in image_parts or []:
        uri = str(part.get("uri") or "").strip()
        if uri:
            locators.append(uri)
            continue
        data = str(part.get("data") or "")
        if not data:
            continue
        mime = str(part.get("mimeType") or "image/png")
        if project and project_dir:
            try:
                raw = base64.b64decode(data, validate=False)
                digest = hashlib.sha256(raw).hexdigest()[:16]
                suffix = mimetypes.guess_extension(mime) or ".png"
                relative = f"inputs/sidecar-ref-{digest}{suffix}"
                target = Path(project_dir) / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    target.write_bytes(raw)
                locators.append(project_static_url(project, relative, target))
                continue
            except (OSError, ValueError):
                pass
        if len(data) <= _MAX_INLINE_REFERENCE_BYTES:
            locators.append(f"data:{mime};base64,{data}")
    return locators


def cancel_active_turns(username: str) -> bool:
    """Cancel the sidecar turns this user has in flight, if any."""

    tasks = [task for task in _ACTIVE_TURNS.get(username, ()) if not task.done()]
    for task in tasks:
        task.cancel()
    return bool(tasks)


async def _stream_assistant_reply_village(
    username: str,
    project: str,
    prompt: str,
    on_event,
    *,
    project_dir: str | None = None,
    project_state_dir: str | None = None,
    conversation_id: str = "default",
    image_parts: list[dict[str, str]] | None = None,
    model: str | None = None,
    agent_model_config: Any | None = None,
    turn_id: str | None = None,
    checkpoint_turn_id: str | None = None,
    canvas_id: str | None = None,
    research_enabled: bool = False,
) -> dict[str, Any]:
    """Run one chat turn through the 小树 sidecar, the only agent brain.

    Canvas/task/workflow writes still go through the native tools and product
    authorities; the sidecar only supplies planning and tool calls.
    """

    from novelvideo.chat import tapcanvas_sidecar

    reference_images = _reference_image_locators(
        image_parts,
        project=project,
        project_dir=project_dir,
    )
    task = asyncio.current_task()
    if task is not None:
        _ACTIVE_TURNS.setdefault(username, set()).add(task)
    try:
        return await tapcanvas_sidecar.stream_chat_turn(
            username,
            project,
            prompt,
            on_event,
            project_dir=project_dir,
            project_state_dir=project_state_dir,
            conversation_id=conversation_id,
            canvas_id=canvas_id,
            turn_id=turn_id,
            model=model,
            reference_images=reference_images,
        )
    finally:
        if task is not None:
            active = _ACTIVE_TURNS.get(username)
            if active is not None:
                active.discard(task)
                if not active:
                    _ACTIVE_TURNS.pop(username, None)
