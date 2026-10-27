"""Deterministic ids shared by Agent preview and canvas persistence."""

from __future__ import annotations

import hashlib


def mint_agent_node_id(command_id: str, seq: int) -> str:
    digest = hashlib.sha1(str(command_id or "cmd").encode("utf-8")).hexdigest()[:12]
    return f"agent-{digest}-{seq}"


def attach_agent_created_node_ids(raw_input: dict) -> dict:
    """Return a preview-safe copy with the ids the persistence layer will use."""
    preview = {
        **raw_input,
        "commands": [
            dict(command) if isinstance(command, dict) else command
            for command in raw_input.get("commands") or []
        ],
    }
    command_id = str(preview.get("command_id") or "").strip()
    commands = preview.get("commands")
    if not command_id or not isinstance(commands, list):
        return preview
    created_types = {
        "annotate",
        "create_canvas_node",
        "create_image_prompt_node",
        "create_video_prompt_node",
        "duplicate_node",
    }
    sequence = 0
    for command in commands:
        if not isinstance(command, dict):
            continue
        command_type = str(command.get("type") or "").strip()
        if command_type not in created_types:
            continue
        sequence += 1
        command.setdefault("created_node_id", mint_agent_node_id(command_id, sequence))
    return preview
